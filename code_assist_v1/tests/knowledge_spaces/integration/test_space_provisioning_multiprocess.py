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
_NAMESPACE = UUID("16fd5a1c-9106-42ca-ae37-26a86c303066")
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_USER_COUNT = 40
_PART_COUNT = 4
_OWNER_COUNT = _USER_COUNT + _PART_COUNT


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.6-space-multiprocess-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(user_id: str, *, role: str = "user", disabled: bool = False):
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p1.6-multiprocess:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status="disabled" if disabled else "active",
        created_at_ms=100,
        updated_at_ms=100,
        disabled_at_ms=100 if disabled else None,
    )


def _seed_40_users_and_four_active_parts(
    factory: ConnectionFactory,
) -> tuple[str, ...]:
    users = (
        _user("admin", role="admin"),
        _user("disabled", disabled=True),
        *(_user(f"user{index:02d}") for index in range(1, 39)),
    )
    assert len(users) == _USER_COUNT
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(connection, users)
            team = repository.create_unit(
                connection,
                unit_id=_uuid("team:concurrency"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Concurrency Team",
                actor_user_id="admin",
                now_ms=1000,
            )
            parts = tuple(
                repository.create_unit(
                    connection,
                    unit_id=_uuid(f"part:{index}"),
                    kind="part",
                    parent_id=team.id,
                    name=f"Concurrency Part {index}",
                    actor_user_id="admin",
                    now_ms=1000 + index,
                ).id
                for index in range(1, _PART_COUNT + 1)
            )
        return parts
    finally:
        connection.close()


_WORKER = r"""
import json
from pathlib import Path
import sqlite3
import sys
import time

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.services.spaces import (
    ProvisionPhase1SpacesCommand,
    SpaceProvisioningService,
    SpaceProvisioningServiceError,
)

database, ready_raw, start_raw, payload_raw = sys.argv[1:]
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
service = SpaceProvisioningService(factory)
ready = Path(ready_raw)
start = Path(start_raw)
ready.touch()
deadline = time.monotonic() + 20.0
while not start.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("start barrier timed out")
    time.sleep(0.01)
try:
    result = service.provision_phase1_spaces(
        ProvisionPhase1SpacesCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=payload["operation_id"],
            request_id=payload["request_id"],
            reason="Two-process phase-1 space reconciliation proof",
            request_meta={"remote_addr": "127.0.0.1"},
        )
    )
    print(json.dumps({"kind": "ok", "result": result.as_dict()}, sort_keys=True))
except sqlite3.OperationalError as exc:
    message = str(exc)
    kind = "sqlite_busy" if "busy" in message.lower() or "locked" in message.lower() else "sqlite_error"
    print(json.dumps({"kind": kind, "message": message}, sort_keys=True))
except SpaceProvisioningServiceError as exc:
    print(json.dumps({
        "error": exc.as_error(),
        "http_status": exc.http_status,
        "kind": "service_error",
    }, sort_keys=True))
"""


def _run_pair(
    layout,
    *,
    label: str,
    payloads: tuple[dict[str, str], dict[str, str]],
) -> tuple[dict[str, object], dict[str, object]]:
    start = layout.root / f"{label}-start"
    ready_paths = (
        layout.root / f"{label}-ready-0",
        layout.root / f"{label}-ready-1",
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
    assert sum(result["kind"] == "sqlite_busy" for result in results) == 0
    return results[0], results[1]


def _without_request_id(result: dict[str, object]) -> dict[str, object]:
    stable = dict(result)
    stable.pop("request_id")
    return stable


def _assert_one_space_per_owner(connection) -> None:
    assert connection.execute(
        "SELECT count(*) FROM knowledge_space WHERE space_kind='personal'"
    ).fetchone()[0] == _USER_COUNT
    assert connection.execute(
        "SELECT count(DISTINCT owner_user_id) FROM knowledge_space "
        "WHERE space_kind='personal'"
    ).fetchone()[0] == _USER_COUNT
    assert connection.execute(
        "SELECT count(*) FROM knowledge_space WHERE space_kind='part'"
    ).fetchone()[0] == _PART_COUNT
    assert connection.execute(
        "SELECT count(DISTINCT owner_org_unit_id) FROM knowledge_space "
        "WHERE space_kind='part'"
    ).fetchone()[0] == _PART_COUNT
    assert connection.execute(
        "SELECT count(*) FROM knowledge_space WHERE space_kind='responsibility'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM knowledge_space"
    ).fetchone()[0] == _OWNER_COUNT + 1
    assert connection.execute(
        "SELECT count(*) FROM knowledge_space "
        "WHERE status<>'active' OR write_enabled<>0 OR generation<>0 OR row_version<>1"
    ).fetchone()[0] == 0


@pytest.mark.scenario("SPACE-005")
@pytest.mark.scenario("SCHEMA-001")
def test_two_process_exact_replay_returns_current_request_ids_and_one_domain_commit(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_40_users_and_four_active_parts(factory)
    operation_id = _uuid("operation:exact-replay")
    request_ids = (
        _uuid("request:exact-replay:0"),
        _uuid("request:exact-replay:1"),
    )

    results = _run_pair(
        isolated_knowledge_layout,
        label="exact-replay",
        payloads=(
            {"operation_id": operation_id, "request_id": request_ids[0]},
            {"operation_id": operation_id, "request_id": request_ids[1]},
        ),
    )

    assert {result["kind"] for result in results} == {"ok"}, results
    domain_results = [result["result"] for result in results]
    assert all(isinstance(result, dict) for result in domain_results)
    assert {result["request_id"] for result in domain_results} == set(request_ids)
    assert {_without_request_id(result)["status"] for result in domain_results} == {
        "created"
    }
    assert _without_request_id(domain_results[0]) == _without_request_id(
        domain_results[1]
    )
    assert domain_results[0]["created_personal_count"] == _USER_COUNT
    assert domain_results[0]["created_part_count"] == _PART_COUNT
    assert len(domain_results[0]["outcomes"]) == _OWNER_COUNT

    connection = factory.open()
    try:
        _assert_one_space_per_owner(connection)
        dedup_rows = connection.execute(
            "SELECT status,result_json FROM operation_dedup WHERE operation_id=?",
            (operation_id,),
        ).fetchall()
        assert len(dedup_rows) == 1 and dedup_rows[0][0] == "committed"
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup"
        ).fetchone()[0] == 1
        stored_result = json.loads(dedup_rows[0][1])
        assert _without_request_id(stored_result) == _without_request_id(
            domain_results[0]
        )
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=? "
            "AND action='knowledge_space.provision_phase1'",
            (operation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=? "
            "AND action='knowledge_space.provisioned'",
            (operation_id,),
        ).fetchone()[0] == _OWNER_COUNT
        assert connection.execute(
            "SELECT count(*) FROM outbox_event "
            "WHERE event_type='knowledge_space.provisioned'"
        ).fetchone()[0] == _OWNER_COUNT
        assert connection.execute(
            "SELECT count(*) FROM audit_event"
        ).fetchone()[0] == _OWNER_COUNT + 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event"
        ).fetchone()[0] == _OWNER_COUNT
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-005")
@pytest.mark.scenario("SCHEMA-001")
def test_two_distinct_operations_race_to_created_and_no_change_without_busy_or_unique_errors(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_40_users_and_four_active_parts(factory)
    operation_ids = (
        _uuid("operation:distinct-race:0"),
        _uuid("operation:distinct-race:1"),
    )
    request_ids = (
        _uuid("request:distinct-race:0"),
        _uuid("request:distinct-race:1"),
    )

    results = _run_pair(
        isolated_knowledge_layout,
        label="distinct-race",
        payloads=tuple(
            {
                "operation_id": operation_id,
                "request_id": request_id,
            }
            for operation_id, request_id in zip(
                operation_ids, request_ids, strict=True
            )
        ),  # type: ignore[arg-type]
    )

    assert {result["kind"] for result in results} == {"ok"}, results
    domain_results = [result["result"] for result in results]
    assert all(isinstance(result, dict) for result in domain_results)
    assert sorted(result["status"] for result in domain_results) == [
        "created",
        "no_change",
    ]
    created = next(result for result in domain_results if result["status"] == "created")
    no_change = next(
        result for result in domain_results if result["status"] == "no_change"
    )
    assert (created["created_personal_count"], created["created_part_count"]) == (
        _USER_COUNT,
        _PART_COUNT,
    )
    assert (no_change["created_personal_count"], no_change["created_part_count"]) == (
        0,
        0,
    )
    assert (
        no_change["existing_personal_count"],
        no_change["existing_part_count"],
    ) == (_USER_COUNT, _PART_COUNT)
    assert {outcome["outcome"] for outcome in created["outcomes"]} == {"created"}
    assert {outcome["outcome"] for outcome in no_change["outcomes"]} == {"existing"}
    assert [outcome["space_id"] for outcome in created["outcomes"]] == [
        outcome["space_id"] for outcome in no_change["outcomes"]
    ]

    connection = factory.open()
    try:
        _assert_one_space_per_owner(connection)
        assert [tuple(row) for row in connection.execute(
            "SELECT status,count(*) FROM operation_dedup "
            "WHERE operation_id IN (?,?) GROUP BY status",
            operation_ids,
        ).fetchall()] == [("committed", 2)]
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM audit_event "
            "WHERE operation_id IN (?,?) "
            "AND action='knowledge_space.provision_phase1'",
            operation_ids,
        ).fetchone()[0] == 2
        provision_audits = connection.execute(
            "SELECT space_id,count(*) FROM audit_event "
            "WHERE action='knowledge_space.provisioned' "
            "GROUP BY space_id ORDER BY space_id"
        ).fetchall()
        assert len(provision_audits) == _OWNER_COUNT
        assert {row[1] for row in provision_audits} == {1}
        provision_outbox = connection.execute(
            "SELECT space_id,count(*) FROM outbox_event "
            "WHERE event_type='knowledge_space.provisioned' "
            "GROUP BY space_id ORDER BY space_id"
        ).fetchall()
        assert len(provision_outbox) == _OWNER_COUNT
        assert {row[1] for row in provision_outbox} == {1}
        assert connection.execute(
            "SELECT count(*) FROM audit_event"
        ).fetchone()[0] == _OWNER_COUNT + 2
        assert connection.execute(
            "SELECT count(*) FROM outbox_event"
        ).fetchone()[0] == _OWNER_COUNT
    finally:
        connection.close()
