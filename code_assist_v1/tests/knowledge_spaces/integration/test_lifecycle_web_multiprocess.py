from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
from code_assist_v1.knowledge_core.repositories.users import UserRepository


pytestmark = [pytest.mark.integration, pytest.mark.concurrency]
_NAMESPACE = UUID("a6871a47-a97d-4f48-baa2-a738afef4c54")
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_COUNT = 40


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _token(label: str) -> str:
    return __import__("base64").urlsafe_b64encode(
        sha256(label.encode("utf-8")).digest()
    ).decode("ascii").rstrip("=")


def _load_lifecycle_fixture_module():
    path = _PACKAGE_ROOT / "tests/knowledge_spaces/unit/test_item_lifecycle_service.py"
    name = "_p57_lifecycle_fixture"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SERVER = r'''
from __future__ import annotations

import json
from pathlib import Path
import sys

from flask import Flask, request
from werkzeug.serving import WSGIRequestHandler, make_server

from code_assist_v1.knowledge_core.canonical_routes import register_canonical_routes
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.feature_flags import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.editing import (
    ImmutableItemEditService,
    LeaseHmacKeyRing,
)
from code_assist_v1.knowledge_core.services.lifecycle import ItemLifecycleService


class _QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        return None

    def log_error(self, format, *args):
        return None


class _Unused:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


database, ready_raw = sys.argv[1:]
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
flags = FeatureFlags(
    core_enabled=True,
    personal_reads=True,
    personal_writes=True,
    part_reads=True,
    part_writes=True,
)
scope = ScopeResolver(flags)
editing = ImmutableItemEditService(
    factory,
    key_ring=LeaseHmacKeyRing(
        active_key_id="lease-v1", keys={"lease-v1": bytes(range(32))}
    ),
    scope_resolver=scope,
)
lifecycle = ItemLifecycleService(factory, scope)
app = Flask("p57-real-web-process")
unused = _Unused()


def principal():
    return {
        "id": request.headers.get("X-Test-User", ""),
        "scope_epoch": int(request.headers.get("X-Test-Scope-Epoch", "0")),
    }


register_canonical_routes(
    app,
    scope_selection_service=unused,
    conversation_service=unused,
    retrieval_service=unused,
    management_read_service=unused,
    agent_context_service=unused,
    turn_executor=unused,
    conversation_summarizer=unused,
    principal_resolver=principal,
    item_edit_service=editing,
    item_lifecycle_service=lifecycle,
)
server = make_server(
    "127.0.0.1", 0, app, threaded=True, request_handler=_QuietHandler
)
Path(ready_raw).write_text(
    json.dumps({"port": server.server_port}), encoding="utf-8"
)
server.serve_forever()
'''


def _start_servers(layout) -> tuple[list[subprocess.Popen[str]], tuple[str, str]]:
    processes: list[subprocess.Popen[str]] = []
    ready_paths = tuple(layout.root / f"p57-web-{index}.json" for index in range(2))
    environment = os.environ.copy()
    for ready in ready_paths:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _SERVER,
                    str(layout.core_db_path),
                    str(ready),
                ],
                cwd=_PACKAGE_ROOT.parent,
                env=environment,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    deadline = time.monotonic() + 30.0
    while not all(path.exists() for path in ready_paths):
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"web worker exited before ready: {process.returncode}; "
                    f"stdout={stdout!r}; stderr={stderr!r}"
                )
        if time.monotonic() >= deadline:
            raise AssertionError("two web processes did not become ready")
        time.sleep(0.02)
    bases = tuple(
        f"http://127.0.0.1:{json.loads(path.read_text(encoding='utf-8'))['port']}"
        for path in ready_paths
    )
    return processes, bases  # type: ignore[return-value]


def _stop_servers(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=15)
        assert stdout == ""
        assert stderr == ""


def _post(
    base: str,
    path: str,
    *,
    user_id: str,
    scope_epoch: int,
    body: dict[str, object],
    token: str | None = None,
    fence: int | None = None,
    expected_revision_id: str | None = None,
) -> tuple[int, dict[str, object]]:
    operation_id = body.get("operation_id")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "p57-isolated-http-client",
        "X-Test-User": user_id,
        "X-Test-Scope-Epoch": str(scope_epoch),
    }
    if isinstance(operation_id, str):
        headers["Idempotency-Key"] = operation_id
    if token is not None:
        headers["X-Knowledge-Lease-Token"] = token
    if fence is not None:
        headers["X-Knowledge-Fence"] = str(fence)
    if expected_revision_id is not None:
        headers["If-Match"] = f'"{expected_revision_id}"'
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    method = (
        "POST"
        if "/lease/" in path or path.endswith(("/deactivate", "/restore"))
        else "PUT"
    )
    last_transport_error: OSError | None = None
    for attempt in range(3):
        request = Request(
            base + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except OSError as exc:
            last_transport_error = exc
            if attempt < 2:
                time.sleep(0.02 * (attempt + 1))
    assert last_transport_error is not None
    return 599, {
        "error": {
            "code": "TRANSPORT_ERROR",
            "message": type(last_transport_error).__name__,
        }
    }


def _error_code(result: tuple[int, dict[str, object]]) -> str | None:
    error = result[1].get("error")
    return error.get("code") if isinstance(error, dict) else None


def _item_state(factory, item_id: str) -> tuple[str, int, str]:
    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            "SELECT lifecycle_status,row_version,current_revision_id "
            "FROM knowledge_item WHERE id=?",
            (item_id,),
        ).fetchone()
        assert row is not None
        return str(row[0]), int(row[1]), str(row[2])
    finally:
        connection.close()


def _edge_checksum(factory) -> str:
    connection = factory.open(read_only=True)
    try:
        payload = {
            "edges": [tuple(row) for row in connection.execute(
                "SELECT * FROM knowledge_edge ORDER BY id"
            ).fetchall()],
            "relations": [tuple(row) for row in connection.execute(
                "SELECT * FROM item_relation ORDER BY id"
            ).fetchall()],
        }
    finally:
        connection.close()
    return sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _count(factory, sql: str, parameters: tuple[object, ...] = ()) -> int:
    connection = factory.open(read_only=True)
    try:
        return int(connection.execute(sql, parameters).fetchone()[0])
    finally:
        connection.close()


def _race(callable_):
    with ThreadPoolExecutor(max_workers=_CLIENT_COUNT) as executor:
        futures = [executor.submit(callable_, index) for index in range(_CLIENT_COUNT)]
        return [future.result(timeout=30) for future in futures]


@pytest.mark.scenario("REV-002")
@pytest.mark.scenario("LEASE-005")
@pytest.mark.scenario("LEASE-006")
@pytest.mark.scenario("LEASE-009")
@pytest.mark.scenario("LEASE-010")
@pytest.mark.scenario("LIFE-001")
@pytest.mark.scenario("LIFE-004")
@pytest.mark.scenario("LIFE-005")
def test_two_real_web_processes_and_forty_clients_close_the_p5_race_gate(
    isolated_knowledge_layout,
) -> None:
    fixture_module = _load_lifecycle_fixture_module()
    fixture = fixture_module._seed(isolated_knowledge_layout)
    fixture_module._seed_graph_and_relation(fixture)
    item_path = f"/api/knowledge/v1/spaces/{fixture.space_id}/items/{fixture.item_id}"
    processes, bases = _start_servers(isolated_knowledge_layout)
    try:
        lifecycle, initial_row_version, initial_revision_id = _item_state(
            fixture.factory, fixture.item_id
        )
        assert lifecycle == "active"
        edge_checksum = _edge_checksum(fixture.factory)
        initial_revision_count = _count(
            fixture.factory, "SELECT count(*) FROM knowledge_revision WHERE item_id=?", (fixture.item_id,)
        )
        initial_in_progress_count = _count(
            fixture.factory,
            "SELECT count(*) FROM operation_dedup WHERE status='in_progress'",
        )

        attempts: list[dict[str, object]] = []
        for index in range(_CLIENT_COUNT):
            attempts.append(
                {
                    "body": {
                        "editor_session_id": _uuid(f"lease-session:{index}"),
                        "operation_id": _uuid(f"lease-operation:{index}"),
                    },
                    "token": _token(f"lease-token:{index}"),
                }
            )
        acquired = _race(
            lambda index: _post(
                bases[index % 2],
                item_path + "/lease/acquire",
                user_id="editor",
                scope_epoch=fixture.epochs["editor"],
                body=attempts[index]["body"],
                token=attempts[index]["token"],
            )
        )
        winners = [index for index, result in enumerate(acquired) if result[0] == 201]
        assert len(winners) == 1, acquired
        assert sum(_error_code(result) == "LEASE_HELD" for result in acquired) == 39
        assert all(result[0] != 500 for result in acquired)
        winner_index = winners[0]
        winner = acquired[winner_index][1]
        winner_token = str(attempts[winner_index]["token"])
        winner_session = str(attempts[winner_index]["body"]["editor_session_id"])
        assert winner_token not in json.dumps(acquired, sort_keys=True)

        force_operation = _uuid("force-release-operation")
        force_release = _post(
            bases[1],
            item_path + "/lease/force-release",
            user_id="leader",
            scope_epoch=fixture.epochs["leader"],
            body={
                "confirmation": "FORCE_RELEASE",
                "operation_id": force_operation,
                "reason": "P5.7 forced stale-save proof",
            },
        )
        assert force_release[0] == 200, force_release
        forced_save = _post(
            bases[0],
            item_path,
            user_id="editor",
            scope_epoch=fixture.epochs["editor"],
            body={
                "editor_session_id": winner_session,
                "expected_revision_id": initial_revision_id,
                "expected_row_version": initial_row_version,
                "lease_id": winner["lease_id"],
                "markdown": "# forced stale save\n",
                "operation_id": _uuid("forced-save-operation"),
                "reason": "must not commit",
            },
            token=winner_token,
            fence=int(winner["fencing_generation"]),
            expected_revision_id=initial_revision_id,
        )
        assert (forced_save[0], _error_code(forced_save)) == (423, "LEASE_LOST")
        assert _count(
            fixture.factory, "SELECT count(*) FROM knowledge_revision WHERE item_id=?", (fixture.item_id,)
        ) == initial_revision_count

        moved_token = _token("moved-token")
        moved_session = _uuid("moved-session")
        moved_acquire = _post(
            bases[0],
            item_path + "/lease/acquire",
            user_id="member",
            scope_epoch=fixture.epochs["member"],
            body={
                "editor_session_id": moved_session,
                "operation_id": _uuid("moved-acquire-operation"),
            },
            token=moved_token,
        )
        assert moved_acquire[0] == 201, moved_acquire
        connection = fixture.factory.open()
        try:
            with transaction(connection):
                organization = OrganizationRepository()
                users = UserRepository()
                membership = organization.get_current_membership(connection, "member")
                assert membership is not None
                organization.close_current_membership(
                    connection,
                    user_id="member",
                    expected_membership_id=membership.id,
                    closed_at_ms=999,
                )
                actor = users.require(connection, "member")
                organization.compare_and_swap_user_part(
                    connection,
                    user_id="member",
                    expected_current_part_id=actor.current_part_id,
                    target_part_id=None,
                    expected_scope_epoch=actor.scope_epoch,
                    expected_row_version=actor.row_version,
                    updated_at_ms=999,
                )
        finally:
            connection.close()
        moved_save = _post(
            bases[1],
            item_path,
            user_id="member",
            scope_epoch=fixture.epochs["member"],
            body={
                "editor_session_id": moved_session,
                "expected_revision_id": initial_revision_id,
                "expected_row_version": initial_row_version,
                "lease_id": moved_acquire[1]["lease_id"],
                "markdown": "# moved stale save\n",
                "operation_id": _uuid("moved-save-operation"),
                "reason": "must not commit",
            },
            token=moved_token,
            fence=int(moved_acquire[1]["fencing_generation"]),
            expected_revision_id=initial_revision_id,
        )
        assert (moved_save[0], _error_code(moved_save)) == (409, "SCOPE_STALE")
        assert _count(
            fixture.factory, "SELECT count(*) FROM knowledge_revision WHERE item_id=?", (fixture.item_id,)
        ) == initial_revision_count

        release_moved = _post(
            bases[0],
            item_path + "/lease/force-release",
            user_id="leader",
            scope_epoch=fixture.epochs["leader"],
            body={
                "confirmation": "FORCE_RELEASE",
                "operation_id": _uuid("release-moved-operation"),
                "reason": "release the moved editor lease",
            },
        )
        assert release_moved[0] == 200, release_moved

        edit_token = _token("edit-token")
        edit_session = _uuid("edit-session")
        edit_acquire = _post(
            bases[1],
            item_path + "/lease/acquire",
            user_id="editor",
            scope_epoch=fixture.epochs["editor"],
            body={
                "editor_session_id": edit_session,
                "operation_id": _uuid("edit-acquire-operation"),
            },
            token=edit_token,
        )
        assert edit_acquire[0] == 201, edit_acquire
        stale_revision = _uuid("not-the-current-revision")
        stale_save = _post(
            bases[0],
            item_path,
            user_id="editor",
            scope_epoch=fixture.epochs["editor"],
            body={
                "editor_session_id": edit_session,
                "expected_revision_id": stale_revision,
                "expected_row_version": initial_row_version,
                "lease_id": edit_acquire[1]["lease_id"],
                "markdown": "# stale revision\n",
                "operation_id": _uuid("stale-save-operation"),
                "reason": "must not commit",
            },
            token=edit_token,
            fence=int(edit_acquire[1]["fencing_generation"]),
            expected_revision_id=stale_revision,
        )
        assert (stale_save[0], _error_code(stale_save)) == (409, "REVISION_CONFLICT")
        edit_audit_before = _count(
            fixture.factory, "SELECT count(*) FROM audit_event WHERE action='knowledge_item.edit'"
        )
        edit_outbox_before = _count(
            fixture.factory, "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_item.edited'"
        )
        save_results = _race(
            lambda index: _post(
                bases[index % 2],
                item_path,
                user_id="editor",
                scope_epoch=fixture.epochs["editor"],
                body={
                    "editor_session_id": edit_session,
                    "expected_revision_id": initial_revision_id,
                    "expected_row_version": initial_row_version,
                    "lease_id": edit_acquire[1]["lease_id"],
                    "markdown": f"# concurrent save {index}\n",
                    "operation_id": _uuid(f"save-race-operation:{index}"),
                    "reason": "P5.7 concurrent save",
                },
                token=edit_token,
                fence=int(edit_acquire[1]["fencing_generation"]),
                expected_revision_id=initial_revision_id,
            )
        )
        assert sum(result[0] == 200 for result in save_results) == 1, save_results
        assert len(
            [
                result
                for result in save_results
                if _error_code(result) in {"LEASE_LOST", "REVISION_CONFLICT"}
            ]
        ) == 39, save_results
        assert all(result[0] != 500 for result in save_results)
        assert _count(
            fixture.factory, "SELECT count(*) FROM knowledge_revision WHERE item_id=?", (fixture.item_id,)
        ) == initial_revision_count + 1
        assert _count(
            fixture.factory, "SELECT count(*) FROM audit_event WHERE action='knowledge_item.edit'"
        ) == edit_audit_before + 1
        assert _count(
            fixture.factory, "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_item.edited'"
        ) == edit_outbox_before + 1

        active, lifecycle_row_version, _current_revision = _item_state(
            fixture.factory, fixture.item_id
        )
        assert active == "active"
        deactivate_audit_before = _count(
            fixture.factory, "SELECT count(*) FROM audit_event WHERE action='knowledge_item.deactivate'"
        )
        deactivate_outbox_before = _count(
            fixture.factory, "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_item.deactivated'"
        )
        deactivated = _race(
            lambda index: _post(
                bases[index % 2],
                item_path + "/deactivate",
                user_id="editor",
                scope_epoch=fixture.epochs["editor"],
                body={
                    "expected_row_version": lifecycle_row_version,
                    "operation_id": _uuid(f"deactivate-race-operation:{index}"),
                    "reason": "P5.7 lifecycle race",
                },
            )
        )
        assert sum(result[0] == 200 for result in deactivated) == 1, deactivated
        assert sum(_error_code(result) == "STATE_CONFLICT" for result in deactivated) == 39
        assert all(result[0] != 500 for result in deactivated)
        assert _edge_checksum(fixture.factory) == edge_checksum
        assert _count(
            fixture.factory, "SELECT count(*) FROM audit_event WHERE action='knowledge_item.deactivate'"
        ) == deactivate_audit_before + 1
        assert _count(
            fixture.factory, "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_item.deactivated'"
        ) == deactivate_outbox_before + 1

        inactive, restore_row_version, _ = _item_state(fixture.factory, fixture.item_id)
        assert inactive == "inactive"
        restore_audit_before = _count(
            fixture.factory, "SELECT count(*) FROM audit_event WHERE action='knowledge_item.restore'"
        )
        restore_outbox_before = _count(
            fixture.factory, "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_item.restored'"
        )
        restored = _race(
            lambda index: _post(
                bases[index % 2],
                item_path + "/restore",
                user_id="editor",
                scope_epoch=fixture.epochs["editor"],
                body={
                    "expected_row_version": restore_row_version,
                    "operation_id": _uuid(f"restore-race-operation:{index}"),
                },
            )
        )
        assert sum(result[0] == 200 for result in restored) == 1, restored
        assert sum(_error_code(result) == "STATE_CONFLICT" for result in restored) == 39
        assert all(result[0] != 500 for result in restored)
        final_lifecycle, _, final_revision = _item_state(fixture.factory, fixture.item_id)
        assert final_lifecycle == "active"
        assert final_revision == _current_revision
        assert _edge_checksum(fixture.factory) == edge_checksum
        assert _count(
            fixture.factory, "SELECT count(*) FROM audit_event WHERE action='knowledge_item.restore'"
        ) == restore_audit_before + 1
        assert _count(
            fixture.factory, "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_item.restored'"
        ) == restore_outbox_before + 1
        assert _count(
            fixture.factory, "SELECT count(*) FROM item_deactivation_episode WHERE item_id=?", (fixture.item_id,)
        ) == 1
        assert _count(
            fixture.factory,
            "SELECT count(*) FROM operation_dedup WHERE status='in_progress'",
        ) == initial_in_progress_count
        print(
            "P5.7_RAW "
            + json.dumps(
                {
                    "client_count": _CLIENT_COUNT,
                    "deactivate_success_count": sum(
                        result[0] == 200 for result in deactivated
                    ),
                    "edge_checksum_after": _edge_checksum(fixture.factory),
                    "edge_checksum_before": edge_checksum,
                    "forced_save_success_count": int(forced_save[0] == 200),
                    "lease_held_count": sum(
                        _error_code(result) == "LEASE_HELD" for result in acquired
                    ),
                    "lease_success_count": len(winners),
                    "moved_save_success_count": int(moved_save[0] == 200),
                    "restore_success_count": sum(
                        result[0] == 200 for result in restored
                    ),
                    "save_race_success_count": sum(
                        result[0] == 200 for result in save_results
                    ),
                    "stale_save_success_count": int(stale_save[0] == 200),
                    "web_process_count": len(processes),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    finally:
        _stop_servers(processes)
