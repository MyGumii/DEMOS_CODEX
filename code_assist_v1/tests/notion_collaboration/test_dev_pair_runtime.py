from __future__ import annotations

import inspect
import json
from http.cookiejar import CookieJar
from pathlib import Path
import socket
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from code_assist_v1.notion_collaboration.dev_pair_runtime import (
    DevPairRuntimeError,
    FORBIDDEN_OPERATOR_PORTS,
    SyntheticPairRuntime,
)
from code_assist_v1.notion_collaboration.pair_config import PairHost
from code_assist_v1.notion_collaboration.synthetic_pair_root import (
    build_synthetic_pair_root,
    load_fixture_credentials,
    verify_synthetic_pair_root,
)


def _request(opener, method: str, url: str, *, origin: str | None = None, payload: Any = None) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if origin is not None:
        headers["Origin"] = origin
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _client():
    return build_opener(HTTPCookieProcessor(CookieJar()))


@pytest.fixture(scope="module")
def running_pair(tmp_path_factory):
    parent = tmp_path_factory.mktemp("d0a-p3-live")
    root = parent / "synthetic-pair"
    build_synthetic_pair_root(root, allowed_parent=parent, created_by="pytest-d0a-p3")
    credentials = {item["id"]: item for item in load_fixture_credentials(root)}
    runtime_root = parent / "pair-runtime"
    runtime = SyntheticPairRuntime(root, runtime_root, allowed_parent=parent)
    receipt = runtime.start()
    yield runtime, root, runtime_root, credentials, receipt
    cleanup = runtime.close()
    assert cleanup == {
        "owned_processes_stopped": True,
        "owned_ports_closed": True,
        "runtime_root_removed": True,
    }


def test_d0a_p3_os_assigned_pair_readiness_and_redacted_attestation(running_pair) -> None:
    runtime, root, runtime_root, credentials, receipt = running_pair
    del credentials
    assert receipt["process_count"] == 2
    assert receipt["distinct_process_ids"] is True
    assert receipt["distinct_os_assigned_ports"] is True
    assert len(set(runtime.process_ids)) == 2
    assert len(set(runtime.ports)) == 2
    assert not (set(runtime.ports) & FORBIDDEN_OPERATOR_PORTS)
    assert receipt["forbidden_operator_ports_accessed"] is False
    assert receipt["production_or_existing_preview_roots_accessed"] is False
    assert receipt["real_identity_imported"] is False
    assert runtime_root.is_dir()
    assert verify_synthetic_pair_root(root)["status"] == "pass"
    serialized = json.dumps(receipt, sort_keys=True)
    secret = (root / "data/.runtime/secret.key").read_text(encoding="utf-8").strip()
    assert secret not in serialized
    assert str(root) not in serialized
    assert str(runtime_root) not in serialized
    assert "password" not in serialized


def test_d0a_p3_same_subject_on_both_hosts_and_independent_subject_contexts(running_pair) -> None:
    runtime, _, _, credentials, _ = running_pair
    code_origin = runtime.origins[PairHost.CODE]
    notion_origin = runtime.origins[PairHost.NOTION]
    owner_client = _client()
    member_client = _client()

    status, document = _request(
        owner_client,
        "POST",
        code_origin + "/api/code/auth/login",
        origin=code_origin,
        payload={"user_id": "fixture-owner", "password": credentials["fixture-owner"]["password"]},
    )
    assert (status, document["subject_id"]) == (200, "fixture-owner")
    for origin in (code_origin, notion_origin):
        status, document = _request(owner_client, "GET", origin + "/__pair__/session")
        assert (status, document["subject_id"]) == (200, "fixture-owner")

    status, document = _request(
        member_client,
        "POST",
        code_origin + "/api/code/auth/login",
        origin=code_origin,
        payload={"user_id": "fixture-member", "password": credentials["fixture-member"]["password"]},
    )
    assert (status, document["subject_id"]) == (200, "fixture-member")
    assert _request(member_client, "GET", notion_origin + "/__pair__/session")[1]["subject_id"] == "fixture-member"
    assert _request(owner_client, "GET", notion_origin + "/__pair__/session")[1]["subject_id"] == "fixture-owner"


def test_d0a_p3_account_authority_origin_boundary_and_local_logout(running_pair) -> None:
    runtime, _, _, credentials, _ = running_pair
    code_origin = runtime.origins[PairHost.CODE]
    notion_origin = runtime.origins[PairHost.NOTION]
    client = _client()
    login_payload = {"user_id": "fixture-page-guest", "password": credentials["fixture-page-guest"]["password"]}

    assert _request(client, "POST", notion_origin + "/api/code/auth/login", origin=notion_origin, payload=login_payload) == (
        403,
        {"error": "ACCOUNT_MUTATION_HOST_REQUIRED"},
    )
    assert _request(client, "POST", code_origin + "/api/code/auth/login", origin=notion_origin, payload=login_payload) == (
        403,
        {"error": "ORIGIN_REJECTED"},
    )
    assert _request(client, "POST", code_origin + "/api/code/auth/login", origin=code_origin, payload=login_payload)[0] == 200
    assert _request(client, "POST", notion_origin + "/__pair__/mutation-probe", origin=code_origin) == (
        403,
        {"error": "ORIGIN_REJECTED"},
    )
    assert _request(client, "POST", notion_origin + "/__pair__/mutation-probe", origin=notion_origin)[0] == 200
    status, document = _request(client, "POST", notion_origin + "/api/code/auth/logout", origin=notion_origin)
    assert (status, document) == (200, {"peer_fanout": False, "status": "logged_out"})
    assert _request(client, "GET", code_origin + "/__pair__/session") == (401, {"error": "AUTHENTICATION_REQUIRED"})


def test_d0a_p3_owned_pid_port_and_marker_bound_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-pair"
    build_synthetic_pair_root(root, allowed_parent=tmp_path, created_by="pytest-cleanup")
    runtime_root = tmp_path / "pair-runtime"
    runtime = SyntheticPairRuntime(root, runtime_root, allowed_parent=tmp_path)
    runtime.start()
    ports = runtime.ports
    assert runtime_root.exists()
    cleanup = runtime.close()
    assert cleanup == {
        "owned_processes_stopped": True,
        "owned_ports_closed": True,
        "runtime_root_removed": True,
    }
    assert not runtime_root.exists()
    for port in ports:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2)
    assert runtime.close() == cleanup


def test_d0a_p3_rejects_existing_or_cross_parent_runtime_root_without_launch(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-pair"
    build_synthetic_pair_root(root, allowed_parent=tmp_path, created_by="pytest-invalid-root")
    existing = tmp_path / "existing-runtime"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(DevPairRuntimeError, match="RUNTIME_ROOT_ALREADY_EXISTS"):
        SyntheticPairRuntime(root, existing, allowed_parent=tmp_path).start()
    assert sentinel.read_text(encoding="utf-8") == "keep"

    other_parent = tmp_path / "other"
    other_parent.mkdir()
    with pytest.raises(DevPairRuntimeError, match="ROOT_PARENT_MISMATCH"):
        SyntheticPairRuntime(root, other_parent / "runtime", allowed_parent=other_parent).start()
    assert not (other_parent / "runtime").exists()


def test_d0a_p3_child_failure_before_readiness_cleans_every_owned_resource(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-pair"
    build_synthetic_pair_root(root, allowed_parent=tmp_path, created_by="pytest-child-fault")
    runtime_root = tmp_path / "pair-runtime"
    runtime = SyntheticPairRuntime(
        root,
        runtime_root,
        allowed_parent=tmp_path,
        fault_before_ready_role=PairHost.CODE,
    )
    with pytest.raises(DevPairRuntimeError, match="CHILD_EXITED_BEFORE_READY"):
        runtime.start()
    assert not runtime_root.exists()
    assert runtime.close() == {
        "owned_processes_stopped": True,
        "owned_ports_closed": True,
        "runtime_root_removed": True,
    }


def test_d0a_p3_public_launcher_has_no_port_source_copy_or_service_switch_authority() -> None:
    parameters = inspect.signature(SyntheticPairRuntime).parameters
    forbidden = {
        "port",
        "code_port",
        "notion_port",
        "source_root",
        "copy_root",
        "service_name",
        "production_root",
        "preview_root",
    }
    assert not (set(parameters) & forbidden)
    assert FORBIDDEN_OPERATOR_PORTS == {10011, 10012, 10111, 10112, 10113}
