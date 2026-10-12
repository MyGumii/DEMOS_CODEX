from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = [pytest.mark.integration, pytest.mark.concurrency]
_NAMESPACE = UUID("1b5229ed-dd0f-45ff-a654-b49a392c2de9")
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.5-multiprocess-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed(factory: ConnectionFactory, *user_ids: str) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(
                connection,
                [
                    LegacyUserRecord(
                        id=user_id,
                        display_name=user_id.title(),
                        password_hash=f"scrypt:p1.5:{user_id}",
                        system_role="admin" if user_id == "admin" else "user",
                        account_status="active",
                        created_at_ms=100,
                        updated_at_ms=100,
                    )
                    for user_id in user_ids
                ],
            )
    finally:
        connection.close()


def _seed_two_parts(factory: ConnectionFactory) -> tuple[str, str]:
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            team_a = repository.create_unit(
                connection,
                unit_id=_uuid("team:a"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team A",
                actor_user_id="admin",
                now_ms=1000,
            )
            part_a = repository.create_unit(
                connection,
                unit_id=_uuid("part:a"),
                kind="part",
                parent_id=team_a.id,
                name="Part A",
                actor_user_id="admin",
                now_ms=1001,
            )
            team_b = repository.create_unit(
                connection,
                unit_id=_uuid("team:b"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team B",
                actor_user_id="admin",
                now_ms=1002,
            )
            part_b = repository.create_unit(
                connection,
                unit_id=_uuid("part:b"),
                kind="part",
                parent_id=team_b.id,
                name="Part B",
                actor_user_id="admin",
                now_ms=1003,
            )
        return part_a.id, part_b.id
    finally:
        connection.close()


_WORKER = r"""
import json
from pathlib import Path
import sys
import time

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.services.organization import (
    CreateOrgUnitCommand,
    OrganizationService,
    OrganizationServiceError,
    SetMembershipCommand,
)
from code_assist_v1.knowledge_core.repositories.organization import RESPONSIBILITY_ROOT_ID

mode, database, ready_raw, start_raw, payload_raw = sys.argv[1:]
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
service = OrganizationService(
    factory,
    preview_hmac_key=b"p1.5 two process preview key!!!!!",
)
ready = Path(ready_raw)
start = Path(start_raw)
ready.touch()
deadline = time.monotonic() + 20.0
while not start.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("start barrier timed out")
    time.sleep(0.01)
try:
    if mode == "create":
        result = service.create_unit(
            CreateOrgUnitCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=payload["operation_id"],
                request_id=payload["request_id"],
                parent_id=RESPONSIBILITY_ROOT_ID,
                kind="team",
                name="Concurrent Team",
                reason="Two-process idempotency proof",
            )
        )
    elif mode == "membership":
        result = service.set_membership(
            SetMembershipCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=payload["operation_id"],
                request_id=payload["request_id"],
                target_user_id="member",
                expected_user_row_version=1,
                part_id=payload["part_id"],
                expected_current_leader_assignment_id=None,
                replacement_leader_user_id=None,
                reason="Two-process membership CAS proof",
            )
        )
    else:
        raise AssertionError(mode)
    print(json.dumps({"kind": "ok", "result": result.as_dict()}, sort_keys=True))
except OrganizationServiceError as exc:
    print(json.dumps({
        "error": exc.as_error(),
        "http_status": exc.http_status,
        "kind": "error",
    }, sort_keys=True))
"""


def _run_pair(
    layout,
    *,
    mode: str,
    payloads: tuple[dict[str, str], dict[str, str]],
) -> tuple[dict[str, object], dict[str, object]]:
    start = layout.root / f"{mode}-start"
    ready_paths = (
        layout.root / f"{mode}-ready-0",
        layout.root / f"{mode}-ready-1",
    )
    processes: list[subprocess.Popen[str]] = []
    environment = os.environ.copy()
    for ready, payload in zip(ready_paths, payloads, strict=True):
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _WORKER,
                    mode,
                    str(layout.core_db_path),
                    str(ready),
                    str(start),
                    json.dumps(payload, sort_keys=True),
                ],
                cwd=_PACKAGE_ROOT.parent,
                env=environment,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    deadline = time.monotonic() + 20.0
    while not all(path.exists() for path in ready_paths):
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"worker exited before barrier: {process.returncode}; "
                    f"stdout={stdout!r}; stderr={stderr!r}"
                )
        if time.monotonic() >= deadline:
            raise AssertionError("workers did not reach the start barrier")
        time.sleep(0.01)
    start.touch()
    results: list[dict[str, object]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, (stdout, stderr)
        assert stderr == ""
        lines = [line for line in stdout.splitlines() if line.strip()]
        assert lines
        results.append(json.loads(lines[-1]))
    return results[0], results[1]


def test_two_processes_replay_one_actor_scoped_operation_exactly_once(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin")
    operation_id = _uuid("operation:two-process-create")
    request_ids = (
        _uuid("request:two-process-create:0"),
        _uuid("request:two-process-create:1"),
    )
    results = _run_pair(
        isolated_knowledge_layout,
        mode="create",
        payloads=(
            {"operation_id": operation_id, "request_id": request_ids[0]},
            {"operation_id": operation_id, "request_id": request_ids[1]},
        ),
    )
    assert {result["kind"] for result in results} == {"ok"}
    domain_results = [result["result"] for result in results]
    assert {item["request_id"] for item in domain_results} == set(request_ids)
    assert len({item["resource_id"] for item in domain_results}) == 1
    assert {item["status"] for item in domain_results} == {"created"}

    connection = factory.open()
    try:
        assert [tuple(row) for row in connection.execute(
            "SELECT status FROM operation_dedup WHERE operation_id=?",
            (operation_id,),
        ).fetchall()] == [("committed",)]
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 1
    finally:
        connection.close()


def test_two_process_membership_competition_has_one_winner_and_one_stale_loser(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    part_a, part_b = _seed_two_parts(factory)
    operation_ids = (
        _uuid("operation:two-process-member:a"),
        _uuid("operation:two-process-member:b"),
    )
    results = _run_pair(
        isolated_knowledge_layout,
        mode="membership",
        payloads=(
            {
                "operation_id": operation_ids[0],
                "part_id": part_a,
                "request_id": _uuid("request:two-process-member:a"),
            },
            {
                "operation_id": operation_ids[1],
                "part_id": part_b,
                "request_id": _uuid("request:two-process-member:b"),
            },
        ),
    )
    assert sorted(result["kind"] for result in results) == ["error", "ok"]
    loser = next(result for result in results if result["kind"] == "error")
    assert loser["http_status"] == 409
    assert loser["error"]["code"] == "REVISION_CONFLICT"

    connection = factory.open()
    try:
        state = OrganizationRepository().get_user_organization_state(
            connection, "member"
        )
        assert state is not None
        assert state.current_part_id in {part_a, part_b}
        assert state.current_part_id == state.membership_part_id
        assert state.scope_epoch == 2 and state.row_version == 2
        assert connection.execute(
            "SELECT count(*) FROM org_membership WHERE user_id=? AND valid_to IS NULL",
            ("member",),
        ).fetchone()[0] == 1
        assert [tuple(row) for row in connection.execute(
            """SELECT status,count(*) FROM operation_dedup
               WHERE operation_id IN (?,?) GROUP BY status ORDER BY status""",
            operation_ids,
        ).fetchall()] == [("committed", 1), ("failed_final", 1)]
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id IN (?,?)",
            operation_ids,
        ).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 2
    finally:
        connection.close()
