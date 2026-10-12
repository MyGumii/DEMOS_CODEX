from __future__ import annotations

from http.cookiejar import CookieJar
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest
from werkzeug.security import generate_password_hash

from code_assist_v1.notion_collaboration import approved_dev_pair as dev_pair_module
from code_assist_v1.notion_collaboration.approved_dev_pair import (
    AGENT_PORT,
    APPROVAL_ID,
    GATEWAY_PORT,
    NOTION_PORT,
    ApprovedDevPairError,
    ApprovedDevPairRuntime,
    prepare_approved_dev_root,
)
from code_assist_v1.notion_collaboration.cross_process_gateway import (
    CrossProcessGatewayError,
    CrossProcessGatewayRuntime,
)


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _source_registry(path: Path) -> tuple[str, dict[str, str]]:
    credentials = {"admin": "admin1234", "user01": "user1234"}
    users = {
        "admin": {
            "id": "admin",
            "display_name": "Admin",
            "role": "admin",
            "disabled": False,
            "password_hash": generate_password_hash(credentials["admin"]),
            "must_change_password": False,
            "created_at": 1,
            "updated_at": 1,
            "personal_note": "must-not-copy",
        },
        "user01": {
            "id": "user01",
            "display_name": "User 01",
            "role": "user",
            "disabled": False,
            "password_hash": generate_password_hash(credentials["user01"]),
            "must_change_password": False,
            "created_at": 2,
            "updated_at": 2,
        },
        "disabled": {
            "id": "disabled",
            "display_name": "Disabled",
            "role": "user",
            "disabled": True,
            "password_hash": generate_password_hash("disabled1234"),
        },
        "service": {
            "id": "service",
            "display_name": "Service",
            "role": "user",
            "disabled": False,
            "service_account": True,
            "password_hash": generate_password_hash("service1234"),
        },
    }
    raw = _canonical({"users": users})
    path.write_bytes(raw)
    import hashlib

    return "sha256:" + hashlib.sha256(raw).hexdigest(), credentials


def _request(client, method: str, url: str, *, origin: str | None = None, payload: Any = None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with client.open(request, timeout=5) as response:
            return response.status, response.headers, json.loads(response.read().decode())
    except HTTPError as exc:
        return exc.code, exc.headers, json.loads(exc.read().decode())


def _client():
    return build_opener(HTTPCookieProcessor(CookieJar()))


def test_prepare_approved_root_imports_only_allowlisted_identity_fields(tmp_path: Path) -> None:
    source = tmp_path / "source-users.json"
    source_sha256, _credentials = _source_registry(source)
    root = tmp_path / "approved-pair"
    marker = prepare_approved_dev_root(
        source,
        root,
        allowed_parent=tmp_path,
        expected_source_sha256=source_sha256,
        allowlist=["admin", "user01"],
        approval_id=APPROVAL_ID,
    )
    imported = json.loads((root / "agent/.runtime/users.json").read_text(encoding="utf-8"))
    assert sorted(imported["users"]) == ["admin", "user01"]
    assert "personal_note" not in imported["users"]["admin"]
    assert not (root / "notion/.runtime/users.json").exists()
    assert not (root / "notion/.runtime/secret.key").exists()
    assert len((root / "agent/.runtime/secret.key").read_text().strip()) == 64
    assert marker["ports"] == {"agent": 10111, "gateway": 10113, "notion": 10112}
    assert marker["identity_allowlist"] == ["admin", "user01"]

    replay = prepare_approved_dev_root(
        source,
        root,
        allowed_parent=tmp_path,
        expected_source_sha256=source_sha256,
        allowlist=["user01", "admin"],
        approval_id=APPROVAL_ID,
    )
    assert replay == marker


@pytest.mark.parametrize("allowlist", [["disabled"], ["service"]])
def test_prepare_approved_root_rejects_non_human_or_disabled_principals(
    tmp_path: Path, allowlist: list[str]
) -> None:
    source = tmp_path / "source-users.json"
    source_sha256, _credentials = _source_registry(source)
    with pytest.raises(ApprovedDevPairError, match="APPROVED_IDENTITY_NOT_ACTIVE_HUMAN"):
        prepare_approved_dev_root(
            source,
            tmp_path / "approved-pair",
            allowed_parent=tmp_path,
            expected_source_sha256=source_sha256,
            allowlist=allowlist,
            approval_id=APPROVAL_ID,
        )


def test_operator_gateway_fixed_port_requires_exact_approval(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    catalog.touch()
    with pytest.raises(CrossProcessGatewayError, match="OPERATOR_FIXED_PORT_APPROVAL_INVALID"):
        CrossProcessGatewayRuntime(
            tmp_path / "runtime",
            allowed_parent=tmp_path,
            catalog_path=catalog,
            user_data_root=tmp_path,
            operator_fixed_port=GATEWAY_PORT,
            operator_approval_id="wrong",
        )
    with pytest.raises(CrossProcessGatewayError, match="OPERATOR_FIXED_PORT_APPROVAL_INVALID"):
        CrossProcessGatewayRuntime(
            tmp_path / "runtime",
            allowed_parent=tmp_path,
            catalog_path=catalog,
            user_data_root=tmp_path,
            operator_fixed_port=41013,
            operator_approval_id=APPROVAL_ID,
        )


def test_stop_rechecks_completed_cleanup_at_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source-users.json"
    source_sha256, _credentials = _source_registry(source)
    root = tmp_path / "approved-pair"
    prepare_approved_dev_root(
        source,
        root,
        allowed_parent=tmp_path,
        expected_source_sha256=source_sha256,
        allowlist=["admin", "user01"],
        approval_id=APPROVAL_ID,
    )
    run_root = root / "runs" / "deadline-race"
    run_root.mkdir(parents=True)
    active_path = root / dev_pair_module.ACTIVE_RUN_NAME
    active_path.write_bytes(
        _canonical(
            {
                "contract": "code_assist.notion.approved_dev_pair_active_run.v1",
                "launch_id": "deadline-race",
                "manager_pid": 4242,
                "root_locator_sha256": dev_pair_module._path_locator(root),
                "run_root_locator_sha256": dev_pair_module._path_locator(run_root),
                "run_root_name": run_root.name,
                "ports": list(dev_pair_module.OPERATOR_PORTS),
            }
        )
    )
    monkeypatch.setattr(dev_pair_module, "_pid_running", lambda _pid: True)
    monkeypatch.setattr(dev_pair_module, "_port_accepting", lambda _port: False)
    write_exclusive = dev_pair_module._write_json_exclusive

    def finish_cleanup(path: Path, value: Any) -> None:
        write_exclusive(path, value)
        active_path.unlink()

    monkeypatch.setattr(dev_pair_module, "_write_json_exclusive", finish_cleanup)
    args = SimpleNamespace(
        root=str(root),
        allowed_parent=str(tmp_path),
        approval_id=APPROVAL_ID,
        timeout_seconds=0.0,
    )

    assert dev_pair_module._stop_main(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "stopped",
        "ports_closed": True,
    }


@pytest.mark.integration
def test_approved_fixed_pair_reuses_identity_isolates_browsers_and_cleans_owned_ports(
    tmp_path: Path,
) -> None:
    for port in (AGENT_PORT, NOTION_PORT, GATEWAY_PORT):
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2)

    source = tmp_path / "source-users.json"
    source_sha256, credentials = _source_registry(source)
    runtime = ApprovedDevPairRuntime(
        source,
        tmp_path / "approved-pair",
        allowed_parent=tmp_path,
        expected_source_sha256=source_sha256,
        allowlist=["admin", "user01"],
        approval_id=APPROVAL_ID,
    )
    try:
        receipt = runtime.start()
        assert receipt["approved_identity_ids"] == ["admin", "user01"]
        assert receipt["new_signing_secret"] is True
        assert receipt["existing_signing_secret_copied"] is False
        assert receipt["personal_content_imported"] is False
        assert receipt["gateway"]["selected_cross_process_runtime"] is True

        agent = f"http://127.0.0.1:{AGENT_PORT}"
        notion = f"http://127.0.0.1:{NOTION_PORT}"
        admin = _client()
        user = _client()
        for client, user_id in ((admin, "admin"), (user, "user01")):
            status, _headers, login = _request(
                client,
                "POST",
                agent + "/api/code/auth/login",
                origin=agent,
                payload={"user_id": user_id, "password": credentials[user_id]},
            )
            assert status == 200
            assert login["user"]["id"] == user_id
            assert _request(client, "GET", agent + "/api/code/auth/me")[2]["user"]["id"] == user_id
            assert _request(client, "GET", notion + "/api/code/auth/me")[2]["user"]["id"] == user_id

        assert _request(admin, "GET", notion + "/api/code/auth/me")[2]["user"]["id"] == "admin"
        assert _request(user, "GET", notion + "/api/code/auth/me")[2]["user"]["id"] == "user01"
        status, _headers, denied = _request(
            admin,
            "POST",
            notion + "/api/code/auth/login",
            origin=notion,
            payload={"user_id": "admin", "password": credentials["admin"]},
        )
        assert status == 403
        assert denied["code"] == "ACCOUNT_MUTATION_HOST_REQUIRED"
        with admin.open(Request(notion + "/code/", method="GET"), timeout=5) as response:
            assert response.url == agent + "/"
        with urlopen(Request(f"http://127.0.0.1:{GATEWAY_PORT}/health"), timeout=5) as response:
            assert json.loads(response.read().decode())["ready_for_admission"] is True
    finally:
        cleanup = runtime.close()
    assert cleanup == {
        "owned_host_processes_stopped": True,
        "owned_gateway_processes_stopped": True,
        "owned_ports_closed": True,
    }
    for port in (AGENT_PORT, NOTION_PORT, GATEWAY_PORT):
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2)
