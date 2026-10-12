from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket

import pytest

from code_assist_v1.notion_collaboration.gateway_service import (
    EXPECTED_RUNTIME_VERSIONS,
    FORBIDDEN_OPERATOR_PORTS,
    GatewayLifecycleProbe,
    GatewayServiceError,
    RuntimeGatewayASGI,
    validate_selected_service_runtime,
)


@pytest.mark.integration
def test_t0b_p2_owned_probe_uses_os_port_and_removes_exact_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "gateway-probe"
    probe = GatewayLifecycleProbe(runtime_root, allowed_parent=tmp_path)
    receipt = probe.start()
    port = receipt["port"]
    assert receipt["os_assigned_port"] is True
    assert port not in FORBIDDEN_OPERATOR_PORTS
    assert runtime_root.is_dir()
    with socket.create_connection(("127.0.0.1", port), timeout=1):
        pass
    cleanup = probe.close()
    assert cleanup == {
        "owned_process_stopped": True,
        "owned_port_closed": True,
        "runtime_root_removed": True,
    }
    assert not runtime_root.exists()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2)


def test_t0b_p2_selected_service_dependency_check_is_exact_and_fail_closed() -> None:
    try:
        observed = validate_selected_service_runtime()
    except GatewayServiceError as exc:
        assert str(exc) == "SELECTED_RUNTIME_NOT_INSTALLED"
    else:
        assert observed == EXPECTED_RUNTIME_VERSIONS


def test_t0b_p2_asgi_health_has_no_identifier_fields() -> None:
    class StubGateway:
        metrics = type("Metrics", (), {"snapshot": staticmethod(lambda: {"contract": "metrics"})})()

        @staticmethod
        def health():
            return {
                "contract": "health",
                "state": "READY",
                "ready_for_admission": True,
                "ready_for_realtime": True,
                "ready_for_compaction": False,
            }

    app = RuntimeGatewayASGI(StubGateway(), compactor_handshake_matches=False)  # type: ignore[arg-type]
    received = iter([{"type": "http.request", "body": b"", "more_body": False}])
    sent: list[dict] = []

    async def receive():
        return next(received)

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "http", "method": "GET", "path": "/health"}, receive, send))
    assert sent[0]["status"] == 200
    document = json.loads(sent[1]["body"].decode("utf-8"))
    assert document["state"] == "READY"
    assert not ({"subject_id", "workspace_id", "page_id", "page_title"} & set(document))
