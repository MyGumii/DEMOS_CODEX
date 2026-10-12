from __future__ import annotations

import json
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    TransactionMode,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.jobs import (
    HandlerKey,
    JobEnqueueDraft,
    JobRepository,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import SessionPrincipal
from code_assist_v1.knowledge_core.services.jobs import (
    CancelDurableJobCommand,
    DurableJobCancellationError,
    DurableJobIdempotencyError,
    DurableJobNotFoundError,
    DurableJobScopeStaleError,
    DurableJobService,
    ReadDurableJobRequest,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("cab041ed-d7a0-4096-a2c5-63ac8480f66f")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.1-durable-job-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_user(
    factory: ConnectionFactory,
    user_id: str,
    *,
    status: str = "active",
) -> None:
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=user_id,
                    display_name=f"Durable {user_id}",
                    password_hash=f"scrypt:p7.1:{user_id}",
                    system_role="user",
                    account_status=status,
                    created_at_ms=100,
                    updated_at_ms=100,
                    disabled_at_ms=100 if status == "disabled" else None,
                ),
            )
    finally:
        connection.close()


def _enqueue_user_job(
    factory: ConnectionFactory,
    user_id: str,
    label: str,
    *,
    cancellable: bool = True,
) -> str:
    job_id = _uuid(f"job:{label}")
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            user = UserRepository().require(connection, user_id)
            now_ms = db_now_ms(connection)
            JobRepository().enqueue(
                connection,
                JobEnqueueDraft(
                    job_id=job_id,
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="user",
                    actor_user_id=user.id,
                    space_id=None,
                    job_type="fixture.public-job",
                    handler_version="v1",
                    effect_key=f"fixture:{label}:v1",
                    input_ref={"record_id": _uuid(f"record:{label}"), "schema_version": 1},
                    expected_state={},
                    authorization_snapshot={
                        "schema_version": 1,
                        "actor_kind": "user",
                        "actor_user_id": user.id,
                        "account_status": user.account_status,
                        "system_role": user.system_role,
                        "current_part_id": user.current_part_id,
                        "scope_epoch": user.scope_epoch,
                        "space_id": None,
                        "capabilities": [],
                    },
                    authorization_scope_epoch=user.scope_epoch,
                    authorization_part_id=user.current_part_id,
                    authorization_capabilities=(),
                    max_attempts=3,
                    cancellable=cancellable,
                    available_at_ms=now_ms,
                ),
            )
    finally:
        connection.close()
    return job_id


def _enqueue_system_job(factory: ConnectionFactory, label: str) -> str:
    job_id = _uuid(f"system-job:{label}")
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            now_ms = db_now_ms(connection)
            JobRepository().enqueue(
                connection,
                JobEnqueueDraft(
                    job_id=job_id,
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="system",
                    actor_user_id=None,
                    space_id=None,
                    job_type="fixture.system-job",
                    handler_version="v1",
                    effect_key=f"fixture:system:{label}:v1",
                    input_ref={"schema_version": 1},
                    expected_state={},
                    authorization_snapshot={"schema_version": 1, "actor_kind": "system"},
                    authorization_scope_epoch=None,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=3,
                    cancellable=True,
                    available_at_ms=now_ms,
                ),
            )
    finally:
        connection.close()
    return job_id


def _principal(user_id: str, epoch: int = 1) -> SessionPrincipal:
    return SessionPrincipal(user_id=user_id, captured_scope_epoch=epoch)


def _cancel(
    service: DurableJobService,
    *,
    user_id: str,
    job_id: str,
    operation: str,
    request: str,
    request_meta: dict[str, object] | None = None,
):
    return service.cancel(
        CancelDurableJobCommand(
            principal=_principal(user_id),
            job_id=job_id,
            operation_id=_uuid(operation),
            request_id=_uuid(request),
            request_meta={} if request_meta is None else request_meta,
        )
    )


def _counts(factory: ConnectionFactory) -> tuple[int, int]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            audit_count = int(
                connection.execute(
                    "SELECT count(*) FROM audit_event WHERE action='durable_job.cancel'"
                ).fetchone()[0]
            )
            operation_count = int(
                connection.execute(
                    "SELECT count(*) FROM operation_dedup WHERE operation_type='durable_job.cancel'"
                ).fetchone()[0]
            )
            return audit_count, operation_count
    finally:
        connection.close()


def test_actor_can_read_only_their_own_user_job(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "alice")
    _seed_user(factory, "bob")
    own = _enqueue_user_job(factory, "alice", "alice")
    foreign = _enqueue_user_job(factory, "bob", "bob")
    system = _enqueue_system_job(factory, "maintenance")
    service = DurableJobService(factory)

    result = service.read(
        ReadDurableJobRequest(
            principal=_principal("alice"),
            job_id=own,
            request_id=_uuid("read-own"),
        )
    )
    assert result.request_id == _uuid("read-own")
    assert result.job.id == own
    assert result.job.actor_user_id == "alice"
    assert result.job.state == "queued"

    for hidden in (foreign, system, _uuid("missing")):
        with pytest.raises(DurableJobNotFoundError):
            service.read(
                ReadDurableJobRequest(
                    principal=_principal("alice"),
                    job_id=hidden,
                    request_id=_uuid(f"read:{hidden}"),
                )
            )


def test_read_and_cancel_reject_stale_or_disabled_session(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "active-user")
    _seed_user(factory, "disabled-user", status="disabled")
    active_job = _enqueue_user_job(factory, "active-user", "active")
    service = DurableJobService(factory)

    with pytest.raises(DurableJobScopeStaleError):
        service.read(
            ReadDurableJobRequest(
                principal=_principal("active-user", 2),
                job_id=active_job,
                request_id=_uuid("stale-read"),
            )
        )
    with pytest.raises(DurableJobScopeStaleError):
        service.cancel(
            CancelDurableJobCommand(
                principal=_principal("disabled-user"),
                job_id=active_job,
                operation_id=_uuid("disabled-cancel"),
                request_id=_uuid("disabled-request"),
            )
        )
    assert _counts(factory) == (0, 0)


def test_queued_cancel_is_atomic_audited_and_exactly_replayable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "cancel-user")
    job_id = _enqueue_user_job(factory, "cancel-user", "queued")
    service = DurableJobService(factory)

    first = _cancel(
        service,
        user_id="cancel-user",
        job_id=job_id,
        operation="cancel-operation",
        request="cancel-request-1",
        request_meta={
            "method": "POST",
            "path": f"/api/knowledge/v1/jobs/{job_id}/cancel",
            "remote_addr": "127.0.0.1",
            "user_agent": "contains-secret-value",
            "authorization": "Bearer must-not-persist",
        },
    )
    assert first.as_dict() == {
        "operation_id": _uuid("cancel-operation"),
        "request_id": _uuid("cancel-request-1"),
        "resource_id": job_id,
        "row_version": None,
        "status": "cancelled",
    }
    assert _counts(factory) == (1, 1)

    second = _cancel(
        service,
        user_id="cancel-user",
        job_id=job_id,
        operation="cancel-operation",
        request="cancel-request-2",
    )
    assert second.request_id == _uuid("cancel-request-2")
    assert second.operation_id == first.operation_id
    assert second.resource_id == first.resource_id
    assert second.status == first.status
    assert _counts(factory) == (1, 1)

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            job = JobRepository().require(connection, job_id)
            assert (job.state, job.cancel_requested, job.completed_at_ms is not None) == (
                "cancelled",
                True,
                True,
            )
            row = connection.execute(
                "SELECT details_json,request_meta_json FROM audit_event WHERE action='durable_job.cancel'"
            ).fetchone()
            assert row is not None
            details = json.loads(row[0])
            request_meta = json.loads(row[1])
            assert details["job_id"] == job_id
            assert request_meta == {
                "method": "POST",
                "path": f"/api/knowledge/v1/jobs/{job_id}/cancel",
                "remote_addr": "127.0.0.1",
            }
            assert "secret" not in row[1].casefold()
            assert "authorization" not in row[1].casefold()
    finally:
        connection.close()


def test_operation_id_cannot_be_reused_for_another_job(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "reuse-user")
    first_job = _enqueue_user_job(factory, "reuse-user", "reuse-first")
    second_job = _enqueue_user_job(factory, "reuse-user", "reuse-second")
    service = DurableJobService(factory)

    _cancel(
        service,
        user_id="reuse-user",
        job_id=first_job,
        operation="reused-operation",
        request="reuse-request-1",
    )
    with pytest.raises(DurableJobIdempotencyError):
        _cancel(
            service,
            user_id="reuse-user",
            job_id=second_job,
            operation="reused-operation",
            request="reuse-request-2",
        )
    assert _counts(factory) == (1, 1)


def test_claimed_cancel_requests_worker_ack_without_running_handler(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "claimed-user")
    job_id = _enqueue_user_job(factory, "claimed-user", "claimed")
    repository = JobRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            claimed = repository.claim_next(
                connection,
                handler_keys=(HandlerKey("fixture.public-job", "v1"),),
                claim_owner="worker-p7",
                lease_ms=60_000,
                now_ms=db_now_ms(connection),
            )
            assert claimed is not None and claimed.job.id == job_id
    finally:
        connection.close()

    result = _cancel(
        DurableJobService(factory),
        user_id="claimed-user",
        job_id=job_id,
        operation="claimed-operation",
        request="claimed-request",
    )
    assert result.status == "cancel_requested"

    connection = factory.open()
    try:
        with transaction(connection):
            requested = repository.require(connection, job_id)
            assert requested.state == "claimed"
            assert requested.cancel_requested is True
            acknowledged = repository.acknowledge_cancel(
                connection,
                claimed.fence,  # type: ignore[union-attr]
                now_ms=db_now_ms(connection),
            )
            assert acknowledged.state == "cancelled"
    finally:
        connection.close()


def test_non_cancellable_job_rolls_back_operation_and_audit(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "fixed-user")
    job_id = _enqueue_user_job(
        factory, "fixed-user", "not-cancellable", cancellable=False
    )

    with pytest.raises(DurableJobCancellationError):
        _cancel(
            DurableJobService(factory),
            user_id="fixed-user",
            job_id=job_id,
            operation="fixed-operation",
            request="fixed-request",
        )
    assert _counts(factory) == (0, 0)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            job = JobRepository().require(connection, job_id)
            assert (job.state, job.cancel_requested) == ("queued", False)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failpoint",
    ("after_job_cancel", "after_audit", "after_operation_commit"),
)
def test_failpoints_roll_back_job_audit_and_idempotency(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "rollback-user")
    job_id = _enqueue_user_job(factory, "rollback-user", failpoint)

    def fail(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(f"failpoint:{name}")

    with pytest.raises(RuntimeError, match=f"failpoint:{failpoint}"):
        _cancel(
            DurableJobService(factory, failpoint=fail),
            user_id="rollback-user",
            job_id=job_id,
            operation=f"operation:{failpoint}",
            request=f"request:{failpoint}",
        )
    assert _counts(factory) == (0, 0)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            job = JobRepository().require(connection, job_id)
            assert (job.state, job.cancel_requested) == ("queued", False)
    finally:
        connection.close()
