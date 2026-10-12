from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.checkpoint import (
    CheckpointOwnerBusyError,
    hold_checkpoint_ownership,
)
from code_assist_v1.knowledge_core.production_service import (
    ProductionServiceNotRunningError,
    WindowsStopEvent,
    stop_event_name,
)


pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.name != "nt", reason="Windows production contract")]
_WORKSPACE_ROOT = Path(__file__).resolve().parents[4]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_json(port: int, process: subprocess.Popen[str]) -> tuple[dict[str, object], str]:
    deadline = time.monotonic() + 30
    last = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise AssertionError(
                f"production WSGI process exited {process.returncode}: {stdout!r} {stderr!r}"
            )
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                payload = json.loads(response.read())
                return payload, str(response.headers.get("Server") or "")
        except HTTPError as exc:
            body = exc.read(512).decode("utf-8", errors="replace")
            last = f"HTTP {exc.code}: {body}"
            time.sleep(0.05)
        except (OSError, URLError, json.JSONDecodeError) as exc:
            last = type(exc).__name__
            time.sleep(0.05)
    raise AssertionError(f"production WSGI readiness timed out: {last}")


_WSGI_CHILD = r'''
import os
import sys
from code_assist_v1.knowledge_core.production_service import WindowsStopEvent
from code_assist_v1.knowledge_core.production_wsgi import ProductionWSGIConfig, ProductionWSGIHost

application_kind, port_text, event_name = sys.argv[1:]
os.environ["CODE_ASSIST_PROCESS_ROLE"] = "web"
os.environ["CODE_ASSIST_STANDALONE"] = "0"
os.environ["CODE_ASSIST_PORT"] = port_text
os.environ["CODE_ASSIST_NOTION_PORT"] = port_text
for name in (
    "KNOWLEDGE_CORE_ENABLED", "KNOWLEDGE_CORE_READS",
    "KNOWLEDGE_CORE_PERSONAL_WRITES", "KNOWLEDGE_CORE_PART_READS",
    "KNOWLEDGE_CORE_PART_WRITES", "KNOWLEDGE_CORE_PROJECTIONS",
    "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2",
    "KNOWLEDGE_CORE_HIGHER_SPACES",
    "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
):
    os.environ[name] = "0"
if application_kind == "code":
    from code_assist_v1.app_code import create_app
else:
    from code_assist_v1.app_notion import create_app
app = create_app()
with WindowsStopEvent.create(event_name) as stop:
    ProductionWSGIHost(
        app,
        ProductionWSGIConfig(
            service_id=application_kind + "-web",
            host="127.0.0.1",
            port=int(port_text),
        ),
    ).serve(stop)
'''


@pytest.mark.parametrize(
    ("application_kind", "service_id", "expected_service"),
    [
        ("code", "CodeAssist-Web", "code_assist_v1"),
        ("notion", "CodeAssist-Notion", "notion_workspace"),
    ],
)
@pytest.mark.scenario("OPS-005")
def test_actual_flask_apps_run_in_independent_waitress_processes_and_drain(
    isolated_knowledge_layout,
    application_kind: str,
    service_id: str,
    expected_service: str,
) -> None:
    port = _free_port()
    event_name = stop_event_name(f"p75-{uuid4().hex[:12]}", service_id)
    environment = dict(os.environ)
    environment["CODE_ASSIST_DATA_ROOT"] = os.fspath(isolated_knowledge_layout.root)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = os.fspath(isolated_knowledge_layout.root)
    environment["CODE_ASSIST_ALLOWED_ORIGINS"] = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-E", "-c", _WSGI_CHILD, application_kind, str(port), event_name],
        cwd=_WORKSPACE_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload, server_header = _wait_json(port, process)
        assert payload["status"] == "ok"
        assert payload["service"] == expected_service
        assert payload["mode"] == "production"
        assert "code-assist-v1" in server_header
        assert "Werkzeug" not in server_header
        WindowsStopEvent.signal_existing(event_name)
        assert process.wait(timeout=45) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


_CHECKPOINT_CHILD = r'''
import sys
import time
from pathlib import Path
from code_assist_v1.knowledge_core.checkpoint import hold_checkpoint_ownership

database, ready, release = sys.argv[1:]
with hold_checkpoint_ownership(database):
    Path(ready).write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 30
    while not Path(release).exists():
        if time.monotonic() >= deadline:
            raise SystemExit(3)
        time.sleep(0.02)
'''


def test_checkpoint_coordinator_mutex_is_machine_wide_across_processes(
    isolated_knowledge_layout,
) -> None:
    ready = isolated_knowledge_layout.root / "checkpoint-owner-ready"
    release = isolated_knowledge_layout.root / "checkpoint-owner-release"
    process = subprocess.Popen(
        [
            sys.executable,
            "-E",
            "-c",
            _CHECKPOINT_CHILD,
            os.fspath(isolated_knowledge_layout.core_db_path),
            os.fspath(ready),
            os.fspath(release),
        ],
        cwd=_WORKSPACE_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file()
        with pytest.raises(CheckpointOwnerBusyError):
            with hold_checkpoint_ownership(isolated_knowledge_layout.core_db_path):
                pass
        release.write_text("release", encoding="ascii")
        assert process.wait(timeout=15) == 0
        with hold_checkpoint_ownership(isolated_knowledge_layout.core_db_path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


_DRAIN_CHILD = r'''
import sys
import time
from pathlib import Path
from code_assist_v1.knowledge_core.production_service import WindowsStopEvent
from code_assist_v1.knowledge_core.production_wsgi import ProductionWSGIConfig, ProductionWSGIHost

port_text, event_name, entered_name, release_name = sys.argv[1:]
entered = Path(entered_name)
release = Path(release_name)
def application(environ, start_response):
    if environ.get("PATH_INFO") != "/slow":
        start_response("404 Not Found", [("Content-Length", "0")])
        return [b""]
    entered.write_text("entered", encoding="ascii")
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("drain fixture release timed out")
        time.sleep(0.01)
    body = b"drained"
    start_response("200 OK", [("Content-Length", str(len(body)))])
    return [body]
with WindowsStopEvent.create(event_name) as stop:
    ProductionWSGIHost(
        application,
        ProductionWSGIConfig("drain-web", "127.0.0.1", int(port_text)),
    ).serve(stop)
'''


@pytest.mark.scenario("OPS-005")
def test_waitress_stop_drains_an_inflight_request_before_process_exit(
    isolated_knowledge_layout,
) -> None:
    port = _free_port()
    event_name = stop_event_name(f"p75-{uuid4().hex[:12]}", "CodeAssist-Web")
    entered = isolated_knowledge_layout.root / "drain-entered"
    release = isolated_knowledge_layout.root / "drain-release"
    process = subprocess.Popen(
        [
            sys.executable,
            "-E",
            "-c",
            _DRAIN_CHILD,
            str(port),
            event_name,
            os.fspath(entered),
            os.fspath(release),
        ],
        cwd=_WORKSPACE_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    result: list[bytes] = []
    failure: list[BaseException] = []

    def request() -> None:
        try:
            with urlopen(f"http://127.0.0.1:{port}/slow", timeout=25) as response:
                result.append(response.read())
        except BaseException as exc:
            failure.append(exc)

    request_thread = threading.Thread(target=request, daemon=False)
    try:
        request_thread.start()
        deadline = time.monotonic() + 15
        while not entered.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert entered.is_file()
        WindowsStopEvent.signal_existing(event_name)
        release.write_text("release", encoding="ascii")
        request_thread.join(timeout=25)
        assert not request_thread.is_alive()
        assert failure == []
        assert result == [b"drained"]
        assert process.wait(timeout=45) == 0
    finally:
        release.touch(exist_ok=True)
        request_thread.join(timeout=2)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_stop_request_for_absent_service_fails_closed() -> None:
    name = stop_event_name(f"p75-{uuid4().hex[:12]}", "CodeAssist-Web")
    with pytest.raises(ProductionServiceNotRunningError):
        WindowsStopEvent.signal_existing(name)
